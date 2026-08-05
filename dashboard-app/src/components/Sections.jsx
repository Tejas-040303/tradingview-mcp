import Excursion from '@/components/Excursion';
import { CalendarHeatmap, Heatmaps } from '@/components/Grids';
import {
  BehaviourPanel, ExitAnalysis, MonthlyReport, SessionCards, SymbolCards, WeekdayBars,
} from '@/components/Breakdowns';

/** Everything below the equity chart, lazy-loaded as one chunk. */
export default function Sections({ data, excursionsEnabled, onEnableExcursions,
  isFetching }) {
  return (
    <>
      <Excursion data={data.excursions} enabled={excursionsEnabled}
        onEnable={onEnableExcursions} isFetching={isFetching} />

      <CalendarHeatmap daily={data.daily} />
      <MonthlyReport daily={data.daily} />
      <SessionCards groups={data.groups} />

      <div className="mb-5 grid gap-4 xl:grid-cols-2">
        <WeekdayBars groups={data.groups} />
        <ExitAnalysis groups={data.groups} />
      </div>

      <Heatmaps heatmaps={data.heatmaps} />
      <SymbolCards groups={data.groups} />
      <BehaviourPanel behaviour={data.behaviour} />
    </>
  );
}
