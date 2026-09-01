import { useState } from "react";
import { CounsellorConsole } from "./CounsellorConsole";
import { DemoBanner } from "./components/DemoBanner";
import { DistrictDashboard } from "./DistrictDashboard";
import "./theme.css";
import "./console.css";

type View = "console" | "district";

export default function App() {
  const [view, setView] = useState<View>("console");

  const apiBase = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

  return (
    <div className="app">
      <DemoBanner apiBase={apiBase} />
      <nav className="app-nav">
        <div className="brand">
          <b>SAMVEDNA</b>
          <span className="lbl">NHAA 14566 · triage support</span>
        </div>
        <div className="tabs">
          <button className={view === "console" ? "on" : ""}
                  onClick={() => setView("console")}>Counsellor</button>
          <button className={view === "district" ? "on" : ""}
                  onClick={() => setView("district")}>District</button>
        </div>
        <span className="disclaimer">
          Decision support. Not a diagnostic service.
        </span>
      </nav>
      {view === "console" ? <CounsellorConsole /> : <DistrictDashboard />}
    </div>
  );
}
