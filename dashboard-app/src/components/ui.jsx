/**
 * Primitives, in the shadcn spirit: source we own rather than a dependency we
 * configure. Kept in one file because there are only a handful and splitting
 * them across a dozen modules buys nothing at this size.
 */
import * as React from 'react';
import * as SelectPrimitive from '@radix-ui/react-select';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { Check, ChevronDown } from 'lucide-react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/api';

export function Card({ className, children, ...props }) {
  return <div className={cn('card p-4 sm:p-5', className)} {...props}>{children}</div>;
}

export function CardTitle({ children, hint, right }) {
  return (
    <div className="mb-3 flex items-start justify-between gap-3">
      <div>
        <h2 className="text-[13px] font-semibold tracking-tight text-ink">{children}</h2>
        {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
      </div>
      {right}
    </div>
  );
}

export function Badge({ children, className, tone = 'neutral' }) {
  const tones = {
    neutral: 'bg-elevated text-muted ring-line',
    up: 'bg-up/10 text-up ring-up/30',
    down: 'bg-down/10 text-down ring-down/30',
    warn: 'bg-warn/10 text-warn ring-warn/30',
    info: 'bg-info/10 text-info ring-info/30',
  };
  return (
    <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5',
      'text-[11px] font-medium ring-1 ring-inset', tones[tone], className)}>
      {children}
    </span>
  );
}

export function Button({ className, variant = 'default', size = 'md', ...props }) {
  const variants = {
    default: 'bg-elevated text-ink hover:bg-line border-line',
    ghost: 'bg-transparent text-muted hover:text-ink hover:bg-elevated border-transparent',
    accent: 'bg-accent text-white hover:opacity-90 border-transparent',
  };
  const sizes = { sm: 'h-7 px-2 text-xs', md: 'h-8 px-3 text-[13px]' };
  return (
    <button
      className={cn('inline-flex items-center justify-center gap-1.5 rounded-lg border',
        'font-medium transition-colors disabled:pointer-events-none disabled:opacity-40',
        variants[variant], sizes[size], className)}
      {...props}
    />
  );
}

export function Select({ value, onValueChange, options, placeholder = 'All', className }) {
  return (
    <SelectPrimitive.Root value={value || '__all'}
      onValueChange={v => onValueChange(v === '__all' ? '' : v)}>
      <SelectPrimitive.Trigger
        className={cn('inline-flex h-8 items-center justify-between gap-2 rounded-lg border',
          'border-line bg-elevated px-2.5 text-[13px] text-ink', className)}>
        <SelectPrimitive.Value placeholder={placeholder} />
        <ChevronDown className="h-3.5 w-3.5 text-muted" />
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          position="popper" sideOffset={6}
          className="z-50 max-h-72 overflow-hidden rounded-xl border border-line bg-surface shadow-lift">
          <SelectPrimitive.Viewport className="p-1">
            {options.map(opt => (
              <SelectPrimitive.Item key={opt.value || '__all'} value={opt.value || '__all'}
                className={cn('relative flex cursor-pointer select-none items-center rounded-lg',
                  'py-1.5 pl-7 pr-3 text-[13px] text-ink outline-none',
                  'data-[highlighted]:bg-elevated')}>
                <span className="absolute left-2 flex h-3.5 w-3.5 items-center">
                  <SelectPrimitive.ItemIndicator><Check className="h-3.5 w-3.5" /></SelectPrimitive.ItemIndicator>
                </span>
                <SelectPrimitive.ItemText>{opt.label}</SelectPrimitive.ItemText>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}

export function Input({ className, ...props }) {
  return (
    <input
      className={cn('h-8 rounded-lg border border-line bg-elevated px-2.5 text-[13px]',
        'text-ink placeholder:text-muted/70', className)}
      {...props}
    />
  );
}

export const Tip = ({ children, content }) => (
  <TooltipPrimitive.Root delayDuration={120}>
    <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content sideOffset={6}
        className="z-50 max-w-xs rounded-lg border border-line bg-surface px-2.5 py-1.5
                   text-xs text-ink shadow-lift">
        {content}
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  </TooltipPrimitive.Root>
);

export const TooltipProvider = TooltipPrimitive.Provider;

export function Skeleton({ className }) {
  return <div className={cn('skeleton', className)} />;
}

/** Uniform empty state, so a panel with no data never looks like a bug. */
export function Empty({ children = 'No data for this selection.', icon: Icon }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      {Icon && <Icon className="h-5 w-5 text-muted/60" />}
      <p className="text-[13px] text-muted">{children}</p>
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-xl border border-down/30 bg-down/5 p-4">
      <p className="text-[13px] font-medium text-down">Could not load this panel</p>
      <p className="text-xs text-muted">{String(error?.message || error)}</p>
      {onRetry && <Button size="sm" onClick={onRetry}>Retry</Button>}
    </div>
  );
}

/** Fade-and-rise on entry, staggered by index. Disabled by reduced-motion CSS. */
export function Reveal({ children, delay = 0, className }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.32, delay, ease: [0.22, 1, 0.36, 1] }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

/**
 * Count up to a target on change.
 *
 * Steps through requestAnimationFrame rather than a spring so the final frame
 * lands on the exact value — a stat tile that settles on 43.09% when the API
 * said 43.1% is a correctness bug, not a cosmetic one.
 */
export function AnimatedNumber({ value, format = v => v.toFixed(2), duration = 550 }) {
  const [shown, setShown] = React.useState(value ?? 0);
  const fromRef = React.useRef(value ?? 0);

  React.useEffect(() => {
    if (value === null || value === undefined || Number.isNaN(value)) return undefined;
    const from = fromRef.current;
    const delta = value - from;
    if (delta === 0) { setShown(value); return undefined; }

    let raf;
    const start = performance.now();
    const tick = now => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - (1 - t) ** 3;
      setShown(t === 1 ? value : from + delta * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
      else fromRef.current = value;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);

  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return format(shown);
}
