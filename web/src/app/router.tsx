import { NavLink, Navigate, Outlet, createBrowserRouter } from "react-router-dom";

import { IncidentListPage } from "../pages/IncidentListPage";
import { EffectPage } from "../pages/EffectPage";
import { ExperiencePage } from "../pages/ExperiencePage";
import { LearningPage } from "../pages/LearningPage";
import { LiveIncidentPage } from "../pages/LiveIncidentPage";

const navigation = [
  { label: "智能诊断", to: "/demo" },
  { label: "事故记录", to: "/incidents" },
  { label: "效果验证", to: "/evaluations" },
  { label: "系统进化", to: "/evolution" },
];

function AppShell() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <NavLink className="brand" to="/demo" aria-label="IncidentPilot \u9996\u9875">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
          <span>
            <strong>IncidentPilot</strong>
            <small>AI 事故响应指挥台</small>
          </span>
        </NavLink>
        <nav className="primary-navigation" aria-label="主导航">
          {navigation.map((item) => <NavLink className={({ isActive }) => isActive ? "active" : ""} key={item.to} to={item.to}>{item.label}</NavLink>)}
        </nav>
        <div className="topbar-actions"><span className="system-state"><i />系统在线</span><NavLink className="console-entry" to="/incidents">专业控制台</NavLink></div>
      </header>
      <main className="workspace"><Outlet /></main>
    </div>
  );
}

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate replace to="/demo" /> },
      { path: "demo", element: <ExperiencePage /> },
      { path: "incidents", element: <IncidentListPage /> },
      { path: "incidents/:incidentId", element: <LiveIncidentPage /> },
      { path: "evaluations", element: <EffectPage /> },
      { path: "evolution", element: <LearningPage /> },
    ],
  },
]);
