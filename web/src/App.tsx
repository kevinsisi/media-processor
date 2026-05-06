import { Navigate, Route, Routes, useParams } from "react-router-dom";
import AppHeader from "./components/AppHeader";
import Health from "./pages/Health";
import NewProject from "./pages/NewProject";
import ProjectAnalysis from "./pages/ProjectAnalysis";
import ProjectEdit from "./pages/ProjectEdit";
import ProjectList from "./pages/ProjectList";
import Settings from "./pages/Settings";
import TimelineEditor from "./pages/TimelineEditor";
import Upload from "./pages/Upload";

function LegacyReviewRedirect() {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={`/projects/${id}/edit`} replace />;
}

export default function App() {
  return (
    <>
      <AppHeader />
      <Routes>
        <Route path="/" element={<ProjectList />} />
        <Route path="/projects/new" element={<NewProject />} />
        <Route path="/projects/:id/upload" element={<Upload />} />
        <Route path="/projects/:id/assets" element={<ProjectAnalysis />} />
        <Route path="/projects/:id/edit" element={<ProjectEdit />} />
        <Route
          path="/projects/:projectId/edit/timeline/:draftId"
          element={<TimelineEditor />}
        />
        <Route path="/projects/:id/review" element={<LegacyReviewRedirect />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/health" element={<Health />} />
      </Routes>
    </>
  );
}
