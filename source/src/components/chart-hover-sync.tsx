import { useEffect, useId, useRef } from "react";
import { useChart } from "@/components/charts/chart-context";

/**
 * Links the hover state of several bklit charts: drop <ChartHoverSync group="x" />
 * inside each chart and hovering any of them moves every crosshair/tooltip in the
 * group to the same data index. Charts in a group must share the same x rows.
 */
type Listener = (index: number | null, source: string) => void;
const groups = new Map<string, Set<Listener>>();

function publish(group: string, index: number | null, source: string) {
  groups.get(group)?.forEach((fn) => fn(index, source));
}

export function ChartHoverSync({ group }: { group: string }) {
  const id = useId();
  const { tooltipData, setTooltipData, data, xScale, xAccessor, lines, yScales, yScale, stackOffsets } = useChart();
  // Index last applied from another chart — not re-published, which would loop.
  const remote = useRef<number | null | undefined>(undefined);

  const index = tooltipData?.index ?? null;
  useEffect(() => {
    if (remote.current !== undefined && remote.current === index) return;
    remote.current = undefined;
    publish(group, index, id);
  }, [group, id, index]);

  useEffect(() => {
    const listener: Listener = (i, source) => {
      if (source === id) return;
      remote.current = i;
      if (i == null || !data[i]) {
        setTooltipData(null);
        return;
      }
      const point = data[i];
      const yPositions: Record<string, number> = {};
      for (const line of lines) {
        const v = point[line.dataKey];
        if (typeof v !== "number") continue;
        const scale = yScales[String(line.yAxisId ?? "left")] ?? yScale;
        const offset = stackOffsets?.get(i)?.get(line.dataKey) ?? 0;
        yPositions[line.dataKey] = scale(v + offset) ?? 0;
      }
      setTooltipData({ point, index: i, x: xScale(xAccessor(point)) ?? 0, yPositions });
    };
    let set = groups.get(group);
    if (!set) groups.set(group, (set = new Set()));
    set.add(listener);
    return () => {
      set!.delete(listener);
    };
  }, [group, id, data, lines, xScale, xAccessor, yScales, yScale, stackOffsets, setTooltipData]);

  return null;
}
