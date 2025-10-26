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
    time: false,  // Disable PM2 timestamps (Python logs already have Beijing time)

    // 环境变量（会自动读取 .env 文件）
    env: {
      NODE_ENV: 'production',
      PYTHONPATH: '.',
      TZ: 'Asia/Shanghai',
    },
    env_file: '.env',

    // 进程管理
    instances: 1,
    exec_mode: 'fork',

    // 错误重启设置
    max_restarts: 10,
    min_uptime: '10s'
  }]
};
