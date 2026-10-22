// Stand-in for next/navigation (App Router hooks) used only by the harness bundle.
import { useEffect, useState } from "react";

const EVT = "pramaan:navigate";
export function navigate(href: string, replace = false) {
  if (replace) history.replaceState(null, "", href);
  else history.pushState(null, "", href);
  window.dispatchEvent(new Event(EVT));
  window.scrollTo({ top: 0 });
}
function useLocation() {
  const [loc, setLoc] = useState(() => window.location.pathname + window.location.search);
  useEffect(() => {
    const on = () => setLoc(window.location.pathname + window.location.search);
    window.addEventListener(EVT, on);
    window.addEventListener("popstate", on);
    return () => {
      window.removeEventListener(EVT, on);
      window.removeEventListener("popstate", on);
    };
  }, []);
  return loc;
}
export function usePathname() {
  return useLocation().split("?")[0];
}
export function useSearchParams() {
  const loc = useLocation();
  return new URLSearchParams(loc.includes("?") ? loc.slice(loc.indexOf("?")) : "");
}
export function useRouter() {
  return { push: (h: string) => navigate(h), replace: (h: string) => navigate(h, true), back: () => history.back(), refresh: () => {} };
}
