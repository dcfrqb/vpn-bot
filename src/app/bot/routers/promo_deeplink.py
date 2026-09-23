"""Deep links /start <code> and /start g_<code> (promo codes, gifts). Must stay right after site_login and before start.

Owner stream: E (Growth & admin). Created empty by Foundation; registered in the order of
app.bot.routers.NEW_ROUTER_MODULES, before every 2.x router.
Handlers here are thin: filters on app.bot.callbacks classes, ports from DI,
texts from app.domain.texts, keyboards from app.bot.views.
"""
from aiogram import Router

router = Router(name="r3_promo_deeplink")
