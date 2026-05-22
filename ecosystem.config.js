/**
 * PM2 process config for the AIPIXQR Node Runner.
 *
 * Usage (photographer's PC, from the Node directory):
 *   npm install -g pm2
 *   pm2 start ecosystem.config.js
 *   pm2 save                        ← persist across reboots
 *   pm2 startup                     ← generate Windows startup command
 *
 * This manages Runner.py (headless worker). APP.py (the GUI) is unaffected —
 * start it separately if you need the account/log UI.
 *
 * PM2 auto-restarts Runner.py within 5 s if it crashes.
 * Logs land in ./logs/pm2-out.log and ./logs/pm2-error.log.
 */
module.exports = {
  apps: [
    {
      name:           'aipixqr-node',
      script:         'Runner.py',
      interpreter:    'venv/Scripts/python.exe',
      cwd:            __dirname,
      watch:          false,
      autorestart:    true,
      restart_delay:  5000,   // ms before restarting after a crash
      max_restarts:   20,     // give up after 20 rapid crashes (config error)
      min_uptime:     '10s',  // must stay up 10 s to count as a successful start
      error_file:     'logs/pm2-error.log',
      out_file:       'logs/pm2-out.log',
      log_date_format:'YYYY-MM-DD HH:mm:ss',
      merge_logs:     true,
      env: {
        PYTHONUNBUFFERED: '1',  // ensure stdout/stderr flush immediately to PM2 logs
      },
    },
  ],
};
