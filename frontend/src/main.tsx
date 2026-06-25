import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { DiagnosticsPage } from "./DiagnosticsPage";
import { LimitUpPage } from "./LimitUpPage";
import { MobileAppPage } from "./MobileAppPage";
import { ReviewPage } from "./ReviewPage";
import { SettingsPage } from "./SettingsPage";
import { WatchPage } from "./WatchPage";
import "./styles.css";

const path = window.location.pathname;
const isNativeApp = Boolean((window as typeof window & { Capacitor?: { isNativePlatform?: () => boolean } }).Capacitor?.isNativePlatform?.());
const Page = isNativeApp || path.includes("app") || window.location.search.includes("app=1")
  ? MobileAppPage
  : path.includes("review")
  ? ReviewPage
  : path.includes("limit-up")
    ? LimitUpPage
    : path.includes("watch")
      ? WatchPage
      : path.includes("settings")
        ? SettingsPage
        : path.includes("diagnostics")
          ? DiagnosticsPage
          : LimitUpPage;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Page />
  </StrictMode>,
);
