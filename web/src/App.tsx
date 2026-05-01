import { Route, Routes } from "react-router-dom";
import AppHeader from "./components/AppHeader";
import Health from "./pages/Health";
import NewProject from "./pages/NewProject";
import ProjectList from "./pages/ProjectList";
import Review from "./pages/Review";
import Upload from "./pages/Upload";

export default function App() {
  return (
    <>
      <AppHeader />
      <Routes>
        <Route path="/" element={<ProjectList />} />
        <Route path="/projects/new" element={<NewProject />} />
        <Route path="/projects/:id/upload" element={<Upload />} />
        <Route path="/projects/:id/review" element={<Review />} />
        <Route path="/health" element={<Health />} />
      </Routes>
    </>
  );
}
