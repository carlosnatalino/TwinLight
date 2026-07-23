import { cn } from "@/lib/cn";

interface StateIndicatorProps {
  label: string;
  value: string;
}

const STATE_COLORS: Record<string, string> = {
  ENABLED: "bg-green-100 text-green-800",
  DISABLED: "bg-red-100 text-red-800",
  UNLOCKED: "bg-green-100 text-green-800",
  LOCKED: "bg-red-100 text-red-800",
  INSTALLED: "bg-blue-100 text-blue-800",
  PLANNED: "bg-gray-100 text-gray-700",
  PENDING_REMOVAL: "bg-orange-100 text-orange-800",
  POTENTIAL_AVAILABLE: "bg-cyan-100 text-cyan-800",
  POTENTIAL_BUSY: "bg-purple-100 text-purple-800",
};

export default function StateIndicator({ label, value }: StateIndicatorProps) {
  const colorClass = STATE_COLORS[value] ?? "bg-gray-100 text-gray-700";

  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</span>
      <span className={cn("inline-flex w-fit rounded-full px-2.5 py-0.5 text-xs font-semibold", colorClass)}>
        {value?.replace(/_/g, " ") ?? "UNKNOWN"}
      </span>
    </div>
  );
}
