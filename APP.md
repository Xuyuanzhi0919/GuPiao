# 打板雷达 App

这个目录已经接入 Capacitor，可以把现有 React 前端打包成 iOS / Android App。

## 运行方式

先启动后端和行情服务：

```bash
./start.sh
```

构建并同步 App 资源：

```bash
cd frontend
npm run app:sync
```

打开 iOS 工程：

```bash
cd frontend
npm run app:ios
```

打开 Android 工程：

```bash
cd frontend
npm run app:android
```

## 手机后端地址

App 首次打开后进入「设置」，填写后端地址：

```text
http://你的Mac局域网IP:8788
```

如果手机不在同一 Wi-Fi，建议用 Tailscale / ZeroTier 的私有地址。

## 移动端入口

浏览器预览移动端页面：

```text
http://localhost:5173/app.html
```

App 原生容器内会自动进入移动端页面。
