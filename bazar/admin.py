from django.contrib import admin
from django.db.models import Count, F, Max, Q, Sum
from django.utils.html import format_html

from .models import Category, Customer, Expense, Order, OrderItem, Product, StoreSettings

admin.site.site_header = "Village Store Admin"

VALID = ~Q(orders__status="cancelled")


@admin.register(StoreSettings)
class StoreSettingsAdmin(admin.ModelAdmin):
    list_display = ("__str__", "festival_mode", "delivery_fee", "free_delivery_above")
    list_editable = ("festival_mode", "delivery_fee", "free_delivery_above")

    def has_add_permission(self, request):
        return not StoreSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("icon", "name", "sort", "is_active")
    list_editable = ("name", "sort", "is_active")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "unit", "price", "festival_price", "cost_price",
                    "margin", "stock_qty", "available", "thumb")
    list_editable = ("price", "festival_price", "cost_price", "stock_qty", "available")
    list_filter = ("category", "available")
    search_fields = ("name",)
    list_per_page = 100
    actions = ["mark_out_of_stock", "mark_in_stock"]

    @admin.display(description="Photo")
    def thumb(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="height:36px;border-radius:6px">', obj.image.url)
        return obj.emoji

    @admin.action(description="Mark selected as out of stock")
    def mark_out_of_stock(self, request, queryset):
        queryset.update(available=False)

    @admin.action(description="Mark selected as in stock")
    def mark_in_stock(self, request, queryset):
        queryset.update(available=True)


class CustomerOrderInline(admin.TabularInline):
    model = Order
    extra = 0
    can_delete = False
    fields = ("id", "created", "status", "payment", "paid", "subtotal", "delivery_fee")
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("phone", "name", "address", "orders_count", "spent", "last_order")
    search_fields = ("phone", "name", "address")
    readonly_fields = ("user", "created", "recent_purchases")
    inlines = [CustomerOrderInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _orders=Count("orders", filter=VALID),
            _spent=Sum(F("orders__subtotal") + F("orders__delivery_fee"), filter=VALID),
            _last=Max("orders__created"),
        )

    @admin.display(description="Orders", ordering="_orders")
    def orders_count(self, obj):
        return obj._orders

    @admin.display(description="Total spent", ordering="_spent")
    def spent(self, obj):
        return obj._spent or 0

    @admin.display(description="Last order", ordering="_last")
    def last_order(self, obj):
        return obj._last

    @admin.display(description="Recent purchases (max 5)")
    def recent_purchases(self, obj):
        if not obj.pk:
            return "-"
        return ", ".join(i.name for i in obj.recent_items(5, only_available=False)) or "-"


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ("name", "unit", "price", "cost_price", "qty")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "phone", "address", "total_amount", "payment", "paid",
                    "status", "delivery_date", "time_slot", "created")
    list_editable = ("status", "paid", "delivery_date", "time_slot")
    list_filter = ("status", "payment", "paid", "delivery_date", "created")
    search_fields = ("name", "phone", "address")
    autocomplete_fields = ("customer",)
    inlines = [OrderItemInline]
    readonly_fields = ("subtotal", "delivery_fee", "created", "delivered_at", "profit")
    actions = ["mark_delivered"]

    @admin.display(description="Total")
    def total_amount(self, obj):
        return obj.total

    @admin.action(description="Mark selected as delivered")
    def mark_delivered(self, request, queryset):
        for order in queryset:      # loop so delivered_at is set in save()
            order.status = Order.Status.DELIVERED
            order.save()


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("date", "title", "kind", "amount")
    list_filter = ("kind", "date")
    search_fields = ("title", "note")
    date_hierarchy = "date"