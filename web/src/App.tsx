import { Route, Routes } from "react-router-dom";
import AppHeader from "./components/AppHeader";
import Health from "./pages/Health";
import ProjectList from "./pages/ProjectList";
import Review from "./pages/Review";

export default function App() {
  return (
    <>
      <AppHeader />
      <Routes>
        <Route path="/" element={<ProjectList />} />
        <Route path="/projects/:id/review" element={<Review />} />
        <Route path="/health" element={<Health />} />
      </Routes>
    </>
  );
}
