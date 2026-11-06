# 足球工作台公网登录

公网入口使用 HTTPS。后端只监听回环地址，反向代理保留原始 Host，转发 `/login`、`/logout`、`/static/console/`、`/api/v1/`、`/api/v2/`；根路径跳转到工作台。登录路径应限制请求频率。不要在代理层对这些路径启用 Basic Auth，也不要通过静态别名绕过应用。

应用读取 `FOOTBALL_SITE_CREDENTIAL` 指向的密码文件，账号为 `football`。生产 systemd 服务使用：

```ini
[Service]
LoadCredential=football-login:/etc/football-console/login-password
Environment=FOOTBALL_SITE_CREDENTIAL=%d/football-login
```

密码源文件仅供服务管理者读取，不存进仓库。设置了凭据但文件不可读或为空时，应用不会启动。未设置变量的本机预览不启用登录，不应直接对公网开放。

上线核验：用新浏览器访问站点，确认进入登录页；未登录请求 API 应返回 401，且不带 WWW-Authenticate；登录后可以打开业务页面；退出后旧会话不能再次读取 API。会话最长一天，服务重启会使已有会话失效。
