module.exports = {
  apps: [{
    name: 'tg-listener',
    script: 'uvx',
    args: ['--with-requirements', 'requirements.txt', 'python', '-m', 'src.listener'],
    cwd: __dirname,
    // 重启策略
    autorestart: true,
    watch: false,
    max_memory_restart: '1G',
    restart_delay: 5000,

    // 日志配置
    log_file: './logs/combined.log',
    out_file: './logs/out.log',
    error_file: './logs/error.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',

    // 环境变量
    env: {
      NODE_ENV: 'production',
      PYTHONPATH: '.'
    },

    // 进程管理
    instances: 1,
    exec_mode: 'fork',

    // 错误重启设置
    max_restarts: 10,
    min_uptime: '10s'
  }]
};
