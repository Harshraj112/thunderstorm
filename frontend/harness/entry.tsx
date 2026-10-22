// Harness entry: renders the SAME components as the Next.js App Router pages, with a tiny path router.
import "../app/globals.css";
import { createRoot } from "react-dom/client";
import Shell from "@/components/Shell";
import Home from "@/components/Home";
import { CasesView, CaseView } from "@/components/Cases";
import EvidenceView from "@/components/Evidence";
import { CaseLogView } from "@/components/CaseLog";
import { usePathname } from "./next-navigation";

const ROUTES: Record<string, () => React.ReactElement> = {
  "/": () => <Home />,
  "/cases/": () => <CasesView />,
  "/case/": () => <CaseView />,
  "/evidence/": () => <EvidenceView />,
  "/log/": () => <CaseLogView />,
};
const TITLES: Record<string, string> = { "/cases/": "Cases", "/case/": "Case", "/evidence/": "Exhibit", "/log/": "Case log" };

function App() {
  let p = usePathname();
  if (!p.endsWith("/")) p += "/";
  const page = ROUTES[p] || ROUTES["/"];
  document.title = (TITLES[p] ? `${TITLES[p]} · ` : "") + "PRAMAAN · Evidence verification";
  return <Shell>{page()}</Shell>;
}

createRoot(document.getElementById("root")!).render(<App />);
