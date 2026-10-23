import { Suspense } from "react";
import EvidenceView from "@/components/Evidence";

export const metadata = { title: "Exhibit · PRAMAAN" };

export default function Page() {
  return (
    <Suspense fallback={<div className="empty">Opening exhibit…</div>}>
      <EvidenceView />
    </Suspense>
  );
}
