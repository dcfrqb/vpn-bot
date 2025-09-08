# CRS VPN Telegram Bot

This repository contains **only the Telegram Bot part** of a larger project.  
The whole system consists of:

- **Remnawave VPN core** — main VPN service and node management  
- **Database & Analytics** — storing users, payments, subscriptions, referrals  
- **YooKassa integration** — secure payment processing  
- **Telegram Bot (this repo)** — user interaction, subscription management, payment handling  

The bot is designed to:
- Provide a simple UI/UX for users via Telegram  
- Connect with Remnawave API to create and deliver VPN configs  
- Handle payments through YooKassa  
- Store subscription data in a PostgreSQL database  
- Offer an admin panel for managing users and plans  

This repo is structured for clean development, GitHub-friendly, and ready for containerized deployment (Docker).  
Secrets such as tokens, API keys, and database credentials are stored in `.env` (not committed to Git).  
