import { RefreshCw } from "lucide-react";
import { cn } from "@/lib/cn";

interface RefreshButtonProps {
  onClick: () => void;
  isLoading?: boolean;
  label?: string;
  className?: string;
}

export default function RefreshButton({
  onClick,
  isLoading = false,
  label = "Refresh",
  className,
}: RefreshButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={isLoading}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium",
        "bg-white border border-gray-300 text-gray-700 shadow-sm",
        "hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed",
        "transition-colors",
        className
      )}
    >
      <RefreshCw className={cn("h-3.5 w-3.5", isLoading && "animate-spin")} />
      {label}
    </button>
  );
}
