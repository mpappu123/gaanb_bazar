import json
import re
from datetime import date, timedelta
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.db import transaction
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import RegisterForm
from .models import Category, Customer, Order, OrderItem, Product, StoreSettings

User = get_user_model()
MAX_QTY = 50


class OrderError(Exception):
    """Raised inside the transaction to roll everything back with a friendly message."""


def _err(msg, status=400):
    return JsonResponse({"error": msg}, status=status)


def _parse_date(value):
    """Delivery date must be today or within the next 7 days, otherwise ignored."""
    try:
        d = date.fromisoformat(str(value))
    except ValueError:
        return None
    today = timezone.localdate()
    return d if today <= d <= today + timedelta(days=7) else None


def home(request):
    store = StoreSettings.load()
    festival = store.festival_mode

    live = Product.objects.filter(available=True).order_by("name")
    categories = list(
        Category.objects.filter(is_active=True)
        .prefetch_related(Prefetch("products", queryset=live, to_attr="live"))
    )
    for c in categories:
        for p in c.live:
            p.display_price = p.get_price(festival)

    recent = []
    customer = getattr(request.user, "customer", None)   # None for guests and staff
    if customer:
        recent = customer.recent_items(5)                # max 5, only items still available
        for it in recent:
            it.product.display_price = it.product.get_price(festival)

    cfg = {
        "shop_name": settings.SHOP_NAME,
        "free_above": store.free_delivery_above,
        "fee": store.delivery_fee,
        "upi_id": settings.UPI_ID,
        "festival": festival,
    }
    return render(request, "store/home.html", {
        "categories": categories, "recent": recent, "customer": customer,
        "cfg": cfg, "shop_name": settings.SHOP_NAME,
    })


def register(request):
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(username=d["phone"], password=d["password"], first_name=d["name"])
            # an old guest customer with the same phone is linked, not duplicated
            customer, _ = Customer.objects.get_or_create(
                phone=d["phone"], defaults={"name": d["name"], "address": d["address"]})
            customer.user, customer.name, customer.address = user, d["name"], d["address"]
            customer.save()
        login(request, user)
        return redirect("home")
    return render(request, "store/register.html", {"form": form})


@require_POST
def create_order(request):
    if not request.user.is_authenticated:
        return _err("Please log in first.", 401)
    customer = getattr(request.user, "customer", None)
    if customer is None:
        return _err("Please log in with a customer account.", 403)

    try:
        data = json.loads(request.body)
    except ValueError:
        return _err("Invalid data.")

    address = str(data.get("address", "")).strip()[:250] or customer.address
    notes = str(data.get("notes", "")).strip()[:250]
    payment = data.get("payment") if data.get("payment") in Order.Payment.values else "cod"
    slot = data.get("time_slot") if data.get("time_slot") in Order.Slot.values else "any"
    delivery_date = _parse_date(data.get("delivery_date")) if data.get("delivery_date") else None
    if not address:
        return _err("Please enter your delivery address.")

    # The client sends only product id and quantity. Prices always come from the database.
    qtys = {}
    for row in data.get("items") or []:
        try:
            pid, q = int(row["id"]), int(row["qty"])
        except (KeyError, TypeError, ValueError):
            continue
        if q > 0:
            qtys[pid] = min(qtys.get(pid, 0) + q, MAX_QTY)
    if not qtys:
        return _err("Your cart is empty.")

    store = StoreSettings.load()
    festival = store.festival_mode

    try:
        with transaction.atomic():
            products = list(
                Product.objects.select_for_update()
                .filter(id__in=qtys.keys(), available=True, category__is_active=True)
            )
            if len(products) != len(qtys):
                raise OrderError("Some items are no longer available. Please refresh the page.")

            order = Order.objects.create(
                customer=customer, name=customer.name, phone=customer.phone, address=address,
                notes=notes, payment=payment, time_slot=slot, delivery_date=delivery_date,
            )
            subtotal, lines = 0, []
            for p in products:
                q = qtys[p.id]
                if not p.reduce_stock(q):
                    raise OrderError(f"Only {p.stock_qty} left of {p.name}.")
                price = p.get_price(festival)
                OrderItem.objects.create(order=order, product=p, name=p.name, unit=p.unit,
                                         price=price, cost_price=p.cost_price, qty=q)
                subtotal += price * q
                lines.append(f"- {p.name} ({p.unit}) x {q} = Rs {price * q}")

            fee = 0 if subtotal >= store.free_delivery_above else store.delivery_fee
            order.subtotal, order.delivery_fee = subtotal, fee
            order.save(update_fields=["subtotal", "delivery_fee"])

            customer.address = address
            customer.save(update_fields=["address"])
    except OrderError as e:
        return _err(str(e))

    pay_txt = "Cash on delivery" if payment == "cod" else "UPI (will send screenshot)"
    msg = [f"*{settings.SHOP_NAME} - Order #{order.id}*", ""] + lines + [
        "",
        f"Delivery: {'Free' if fee == 0 else 'Rs ' + str(fee)}",
        f"*Total: Rs {order.total}*",
        f"Payment: {pay_txt}",
    ]
    if delivery_date:
        msg.append(f"Delivery date: {delivery_date:%d %b}")
    if slot != "any":
        msg.append(f"Time: {order.get_time_slot_display()}")
    if notes:
        msg.append(f"Note: {notes}")
    msg += ["", f"Name: {customer.name}", f"Phone: {customer.phone}", f"Address: {address}"]

    url = f"https://wa.me/{settings.WHATSAPP_NUMBER}?text={quote(chr(10).join(msg))}"
    return JsonResponse({"order_id": order.id, "total": order.total, "whatsapp_url": url})