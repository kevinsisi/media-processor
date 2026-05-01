import { Link, NavLink } from "react-router-dom";
import "./AppHeader.css";

export default function AppHeader() {
  return (
    <header className="app-header">
      <div className="app-header__inner">
        <Link to="/" className="app-header__brand">
          <span className="brand-mark">M·P</span>
          <span className="brand-word">
            Media <span className="em">·</span> Processor
          </span>
        </Link>

        <nav className="app-header__nav">
          <NavLink to="/" end className="nav-link">
            草稿清單
          </NavLink>
          <NavLink to="/health" className="nav-link nav-link--quiet">
            系統狀態
          </NavLink>
        </nav>
      </div>
      <div className="app-header__rule" aria-hidden />
    </header>
  );
}
