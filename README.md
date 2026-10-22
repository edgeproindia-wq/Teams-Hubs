# EdgeChat - company internal messaging (Django + WebSockets)

Features: register (create company / join with code), login, dashboard, active employee list
(green dot = online), search by name, real-time private chat, AI copilot sidebar, admin panel
(all employees, status, roles, all company messages), strict per-company data isolation.

## Run on your PC (Windows PowerShell)
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    $env:DEBUG="1"
    python manage.py migrate
    python manage.py runserver        # open http://127.0.0.1:8000
Open two different browsers (or one normal + one private window), register two users in the same
company (second one uses the join code from Admin page) and chat.

## Deploy on Railway (gives you the HTTPS link)
1. Push this folder to a NEW GitHub repo (do not use your existing project repos).
2. Railway > New Project > Deploy from GitHub repo.
3. Add a PostgreSQL database to the project (Railway sets DATABASE_URL for the web service; if not,
   add a variable reference to it).
4. Web service > Variables: SECRET_KEY = long random text, DEBUG = 0,
   optional ANTHROPIC_API_KEY (real AI copilot).
5. Web service > Settings > Networking > Generate Domain. That https://....up.railway.app is your link.
6. Keep ONE instance. To run more than one, add a Redis plugin and set REDIS_URL.

## Notes
- First person who registers with "Create a new company" becomes the company admin.
- Employees register with "Join my company" + the 8-character code shown on the Admin page.
- Admins can read all messages of their own company (read-only); users are told this on screen.
- Without ANTHROPIC_API_KEY the copilot still works with a basic offline drafter.
- Tests: `python manage.py test chat`
