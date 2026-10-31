import { lazy, Suspense } from "react";

const DesktopExperience = lazy(() =>
  import("./components/DesktopExperience").then((module) => ({
    default: module.DesktopExperience,
  })),
);
const PhoneCapture = lazy(() =>
  import("./components/PhoneCapture").then((module) => ({ default: module.PhoneCapture })),
);

export default function App() {
  const Experience = location.pathname.startsWith("/capture")
    ? PhoneCapture
    : DesktopExperience;
  return (
    <Suspense fallback={<main className="centered">Loading GaussianCircuit…</main>}>
      <Experience />
    </Suspense>
  );
}
