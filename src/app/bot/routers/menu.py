"""Main menu / status card, Nav(s=main|plan|help ...), stateless Back and Refresh.

Owner stream: D (User UI). Created empty by Foundation; registered in the order of
app.bot.routers.NEW_ROUTER_MODULES, before every 2.x router.
Handlers here are thin: filters on app.bot.callbacks classes, ports from DI,
texts from app.domain.texts, keyboards from app.bot.views.
"""
from aiogram import Router

router = Router(name="r3_menu")
