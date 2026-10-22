import { Suspense } from "react";
import { CaseView } from "@/components/Cases";

export const metadata = { title: "Case · PRAMAAN" };

// Static export: the case id travels in ?id=, read on the client inside a Suspense boundary.
export default function Page() {
  return (
    <Suspense fallback={<div className="empty">Opening case…</div>}>
      <CaseView />
    </Suspense>
  );
}
