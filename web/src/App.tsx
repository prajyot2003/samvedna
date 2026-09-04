import { useState } from "react";
import { CounsellorConsole } from "./CounsellorConsole";
import { DistrictDashboard } from "./DistrictDashboard";
import "./theme.css";
import "./console.css";

type View = "console" | "district";

export default function App() {
  const [view, setView] = useState<View>("console");

  return (
    <div className="app">
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
