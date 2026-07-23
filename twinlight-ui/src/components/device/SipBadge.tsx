import { Link2 } from "lucide-react";

interface SipBadgeProps {
  sipUuid: string;
}

export default function SipBadge({ sipUuid }: SipBadgeProps) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 border border-blue-200">
      <Link2 className="h-3 w-3" />
      SIP:{sipUuid.slice(0, 8)}…
    </span>
  );
}
