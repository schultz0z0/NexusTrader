from stock_indicators import indicators
from stock_indicators import Quote
from stock_indicators.indicators.common.enums import EndType
import datetime

# Create dummy quotes
quotes = []
base_price = 100
for i in range(50):
    price = base_price + (i % 10)
    quotes.append(Quote(
        date=datetime.datetime(2023, 1, 1) + datetime.timedelta(minutes=i),
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=100
    ))

donchian = indicators.get_donchian(quotes, 21)
zigzag = indicators.get_zig_zag(quotes, 1.0)  # default is Percent

print("Donchian last:", donchian[-1].upper_band, donchian[-1].lower_band)
print("ZigZag points:")
for z in zigzag:
    if z.point_type:
        print(f"Date: {z.date}, Type: {z.point_type}, Value: {z.value}")
