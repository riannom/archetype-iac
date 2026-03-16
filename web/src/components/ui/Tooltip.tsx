import React, { useState, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';

export type TooltipPlacement = 'top' | 'bottom' | 'left' | 'right';

export interface TooltipProps {
  content: React.ReactNode;
  placement?: TooltipPlacement;
  delay?: number;
  children: React.ReactElement;
  className?: string;
}

export const Tooltip: React.FC<TooltipProps> = ({
  content,
  placement = 'top',
  delay = 300,
  children,
  className = '',
}) => {
  const [visible, setVisible] = useState(false);
  const [coords, setCoords] = useState({ x: 0, y: 0 });
  const timeoutRef = useRef<number | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  const show = useCallback(() => {
    timeoutRef.current = window.setTimeout(() => {
      if (!triggerRef.current) return;
      const rect = triggerRef.current.getBoundingClientRect();
      const gap = 8;

      let x: number, y: number;
      switch (placement) {
        case 'bottom':
          x = rect.left + rect.width / 2;
          y = rect.bottom + gap;
          break;
        case 'left':
          x = rect.left - gap;
          y = rect.top + rect.height / 2;
          break;
        case 'right':
          x = rect.right + gap;
          y = rect.top + rect.height / 2;
          break;
        case 'top':
        default:
          x = rect.left + rect.width / 2;
          y = rect.top - gap;
          break;
      }
      setCoords({ x, y });
      setVisible(true);
    }, delay);
  }, [placement, delay]);

  const hide = useCallback(() => {
    if (timeoutRef.current) {
      window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    setVisible(false);
  }, []);

  const transformOrigin: Record<TooltipPlacement, string> = {
    top: 'origin-bottom',
    bottom: 'origin-top',
    left: 'origin-right',
    right: 'origin-left',
  };

  return (
    <>
      {React.cloneElement(children, {
        ref: (node: HTMLElement | null) => {
          triggerRef.current = node;
          const childRef = (children as React.RefAttributes<HTMLElement>).ref;
          if (typeof childRef === 'function') childRef(node);
          else if (childRef && typeof childRef === 'object') {
            (childRef as React.MutableRefObject<HTMLElement | null>).current = node;
          }
        },
        onMouseEnter: (e: React.MouseEvent) => {
          show();
          children.props.onMouseEnter?.(e);
        },
        onMouseLeave: (e: React.MouseEvent) => {
          hide();
          children.props.onMouseLeave?.(e);
        },
        onFocus: (e: React.FocusEvent) => {
          show();
          children.props.onFocus?.(e);
        },
        onBlur: (e: React.FocusEvent) => {
          hide();
          children.props.onBlur?.(e);
        },
      })}
      {visible && content && createPortal(
        <div
          className={`
            fixed z-[200] pointer-events-none
            animate-in fade-in zoom-in-95 duration-100 ${transformOrigin[placement]}
            ${className}
          `.trim().replace(/\s+/g, ' ')}
          style={{
            left: coords.x,
            top: coords.y,
            transform: placement === 'top' ? 'translate(-50%, -100%)'
              : placement === 'bottom' ? 'translate(-50%, 0)'
              : placement === 'left' ? 'translate(-100%, -50%)'
              : 'translate(0, -50%)',
          }}
        >
          <div className="px-2.5 py-1.5 text-xs font-medium text-white bg-stone-900 dark:bg-stone-700 rounded-md shadow-lg whitespace-nowrap max-w-xs">
            {content}
          </div>
        </div>,
        document.body
      )}
    </>
  );
};

export default Tooltip;
