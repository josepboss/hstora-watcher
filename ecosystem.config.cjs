module.exports = {
  apps: [{
    name: "hstora-watcher",
    script: ".venv/bin/hstora-watcher",
    args: "dashboard",
    cwd: __dirname,
    interpreter: "none",
    autorestart: true,
    restart_delay: 5000,
    max_restarts: 20,
    time: true,
    env: { PYTHONUNBUFFERED: "1" }
  }]
};
