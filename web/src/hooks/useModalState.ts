import { useState, useCallback } from 'react';

/**
 * Return type for useModalState.
 *
 * When T is `void`, `open()` takes no arguments and `data` is always `undefined`.
 * When T is a concrete type, `open(data)` requires the data argument and `data`
 * holds the value while the modal is open (null when closed).
 */
export interface ModalState<T> {
  isOpen: boolean;
  data: T | null;
  open: T extends void ? () => void : (data: T) => void;
  close: () => void;
}

/**
 * Generic hook for modal open/close state with optional associated data.
 *
 * Usage without data (simple boolean toggle):
 *   const logsModal = useModalState();
 *   logsModal.open();     // opens
 *   logsModal.close();    // closes
 *   logsModal.isOpen;     // boolean
 *
 * Usage with data:
 *   const deleteModal = useModalState<HostDetailed>();
 *   deleteModal.open(host);   // opens with data
 *   deleteModal.data;         // HostDetailed | null
 *   deleteModal.close();      // closes and clears data
 */
export function useModalState<T = void>(): ModalState<T> {
  const [isOpen, setIsOpen] = useState(false);
  const [data, setData] = useState<T | null>(null);

  const open = useCallback((value?: T) => {
    if (value !== undefined) {
      setData(value);
    }
    setIsOpen(true);
  }, []) as ModalState<T>['open'];

  const close = useCallback(() => {
    setIsOpen(false);
    setData(null);
  }, []);

  return { isOpen, data, open, close };
}
