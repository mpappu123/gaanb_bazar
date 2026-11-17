from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Sum
from django.utils import timezone

phone_validator = RegexValidator(r"^\d{10}$", "Enter a 10 digit mobile number.")


class StoreSettings(models.Model):
    """Single row. Edit from admin instead of changing code."""
    festival_mode = models.BooleanField(default=False, help_text="ON = products with a festival price use it")
    delivery_fee = models.PositiveIntegerField(default=15)
    free_delivery_above = models.PositiveIntegerField(default=200)

    class Meta:
        verbose_name_plural = "store settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Store settings"


class Category(models.Model):
    name = models.CharField(max_length=50)
    icon = models.CharField(max_length=8, default="🛒", help_text="Emoji shown on the category button")
    sort = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, help_text="Untick to hide the whole category (off-season items)")

    class Meta:
        ordering = ["sort", "id"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Product(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="products")
    name = models.CharField(max_length=80)
    unit = models.CharField(max_length=30, help_text="e.g. 1 kg, 1 dozen, 1 piece")
    price = models.PositiveIntegerField(help_text="Normal selling price in rupees")
    festival_price = models.PositiveIntegerField(null=True, blank=True, help_text="Used only when Festival mode is ON")
    cost_price = models.PositiveIntegerField(null=True, blank=True, help_text="Your buying price (customer never sees this)")
    stock_qty = models.PositiveIntegerField(null=True, blank=True, help_text="Leave empty if you don't track stock. At 0 the item hides automatically")
    emoji = models.CharField(max_length=8, default="🛒", blank=True, help_text="Used when there is no photo")
    image = models.ImageField(upload_to="products/", blank=True)
    available = models.BooleanField(default=True)

    class Meta:
        ordering = ["category__sort", "name"]

    def save(self, *args, **kwargs):
        if self.stock_qty == 0:
            self.available = False
        super().save(*args, **kwargs)

    def get_price(self, festival=None):
        """Pass festival=StoreSettings.load().festival_mode once from the view, to avoid a query per product."""
        if festival is None:
            festival = StoreSettings.load().festival_mode
        return self.festival_price if (festival and self.festival_price) else self.price

    def reduce_stock(self, qty):
        """Call inside the order transaction. Returns False if there is not enough stock."""
        if self.stock_qty is None:
            return True
        if qty > self.stock_qty:
            return False
        self.stock_qty -= qty
        self.save(update_fields=["stock_qty", "available"])
        return True

    @property
    def margin(self):
        return None if self.cost_price is None else self.price - self.cost_price

    def __str__(self):
        return f"{self.name} ({self.unit})"


class Customer(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                null=True, blank=True, related_name="customer")
    phone = models.CharField(max_length=10, unique=True, validators=[phone_validator])
    name = models.CharField(max_length=100)
    address = models.CharField(max_length=250, blank=True, help_text="Last used address")
    notes = models.CharField(max_length=250, blank=True, help_text="Private note, e.g. 'call before coming'")
    created = models.DateTimeField(auto_now_add=True)

    def _valid_orders(self):
        return self.orders.exclude(status=Order.Status.CANCELLED)

    @property
    def total_orders(self):
        return self._valid_orders().count()

    @property
    def total_spent(self):
        return self._valid_orders().aggregate(t=Sum(F("subtotal") + F("delivery_fee")))["t"] or 0

    def recent_items(self, limit=5, only_available=True):
        """Last distinct products this customer bought (max `limit`), newest first."""
        rows = (OrderItem.objects.filter(order__customer=self)
                .exclude(order__status=Order.Status.CANCELLED)
                .select_related("product", "product__category")
                .order_by("-order__created", "-id"))
        seen, out = set(), []
        for it in rows.iterator():
            p = it.product
            if p is None or p.id in seen:
                continue
            if only_available and not (p.available and p.category.is_active):
                continue
            seen.add(p.id)
            out.append(it)
            if len(out) == limit:
                break
        return out

    def __str__(self):
        return f"{self.name} ({self.phone})"


class Order(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        CONFIRMED = "confirmed", "Confirmed"
        OUT = "out", "Out for delivery"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    class Payment(models.TextChoices):
        COD = "cod", "Cash on delivery"
        UPI = "upi", "UPI"

    class Slot(models.TextChoices):
        ANY = "any", "Any time"
        MORNING = "morning", "Morning (8-12)"
        AFTERNOON = "afternoon", "Afternoon (12-4)"
        EVENING = "evening", "Evening (4-8)"

    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    # Snapshot of contact details at order time
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=10, validators=[phone_validator])
    address = models.CharField(max_length=250)
    notes = models.CharField(max_length=250, blank=True, help_text="Customer's note for this order")
    payment = models.CharField(max_length=5, choices=Payment.choices, default=Payment.COD)
    paid = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW)
    delivery_date = models.DateField(null=True, blank=True)
    time_slot = models.CharField(max_length=10, choices=Slot.choices, default=Slot.ANY)
    subtotal = models.PositiveIntegerField(default=0)
    delivery_fee = models.PositiveIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]

    def save(self, *args, **kwargs):
        if self.status == self.Status.DELIVERED:
            if not self.delivered_at:
                self.delivered_at = timezone.now()
        else:
            self.delivered_at = None
        super().save(*args, **kwargs)

    @property
    def total(self):
        return self.subtotal + self.delivery_fee

    @property
    def profit(self):
        """Needs cost price on every item, otherwise None."""
        items = list(self.items.all())
        if not items or any(i.cost_price is None for i in items):
            return None
        return sum((i.price - i.cost_price) * i.qty for i in items) + self.delivery_fee

    def __str__(self):
        return f"#{self.id} {self.name}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    # Snapshot at order time, so later price changes don't alter old orders
    name = models.CharField(max_length=80)
    unit = models.CharField(max_length=30)
    price = models.PositiveIntegerField()
    cost_price = models.PositiveIntegerField(null=True, blank=True)
    qty = models.PositiveIntegerField()

    @property
    def line_total(self):
        return self.price * self.qty

    def __str__(self):
        return f"{self.name} x {self.qty}"


class Expense(models.Model):
    class Kind(models.TextChoices):
        FUEL = "fuel", "Petrol / transport"
        PACKING = "packing", "Packaging"
        SUPPLIER = "supplier", "Supplier transport"
        MARKETING = "marketing", "Posters / promotion"
        TECH = "tech", "Hosting / internet"
        OTHER = "other", "Other"

    date = models.DateField(default=timezone.localdate)
    title = models.CharField(max_length=100)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.OTHER)
    amount = models.PositiveIntegerField(help_text="In rupees")
    note = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.title} - Rs {self.amount}"