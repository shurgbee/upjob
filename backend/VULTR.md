Because this deployment uses user-level systemd services, the normal backend update is:

ssh vultr-upjob

cd ~/upjob
git pull --ff-only origin main

cd backend
.venv/bin/python -m pip install -r requirements.txt

systemctl --user restart upjob-api upjob-worker
systemctl --user --no-pager status upjob-api upjob-worker

curl --fail <http://127.0.0.1:8000/health>
curl --fail <https://api.upjob.work/health>

If database migrations changed:

cd ~/upjob/frontend
set -a
source ../backend/.env
set +a

~/.bun/bin/bun install --frozen-lockfile
~/.bun/bin/bun run scripts/migrate-resume.mjs --check
~/.bun/bin/bun run scripts/migrate-resume.mjs --apply

systemctl --user restart upjob-api upjob-worker

If the LaTeX compiler files changed:

cd ~/upjob
docker build -t upjob-tex:local backend/resume-compiler
systemctl --user restart upjob-worker

To inspect problems:

journalctl --user -u upjob-api -u upjob-worker -n 200 --no-pager
journalctl --user -u upjob-api -f
journalctl --user -u upjob-worker -f

A convenient all-purpose update sequence is:

ssh vultr-upjob

cd ~/upjob &&
git pull --ff-only origin main &&
cd backend &&
.venv/bin/python -m pip install -r requirements.txt &&
systemctl --user restart upjob-api upjob-worker &&
sleep 6 &&
curl --fail <https://api.upjob.work/health>

The frontend is separate: because upjob.work points to Vercel, pushing to the branch configured in Vercel should redeploy it automatically. Ensure Vercel has:

RESUME_BACKEND_URL=<https://api.upjob.work>
RESUME_SERVICE_TOKEN=<same value as backend/.env>

If an update breaks production, roll back to the previously known commit:

cd ~/upjob
git log --oneline -10
git switch --detach PREVIOUS_COMMIT_SHA
systemctl --user restart upjob-api upjob-worker

Then return to current main later with:

git switch main
git pull --ff-only origin main
systemctl --user restart upjob-api upjob-worker
