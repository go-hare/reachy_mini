import { useState, useCallback, useEffect } from 'react';
import useAppStore from '../store/useAppStore';

/**
 * 🍞 Global toast hook - uses Zustand store for centralized notifications
 *
 * All components share the SAME toast state, preventing duplicate toasts.
 * Progress bar animation is handled locally for performance.
 *
 * @returns {Object} Toast state and controls
 * @returns {Object} return.toast - Toast state { open, message, severity }
 * @returns {number} return.toastProgress - Progress bar percentage (0-100)
 * @returns {Function} return.showToast - Show toast with message and severity
 * @returns {Function} return.handleCloseToast - Close toast
 *
 * @example
 * const { toast, toastProgress, showToast, handleCloseToast } = useToast();
 *
 * // Show success toast
 * showToast('Update completed!', 'success');
 *
 * // Show error toast
 * showToast('Connection failed', 'error');
 */
export function useToast() {
  // 🎯 Global toast state from Zustand store
  const toast = useAppStore(state => state.toast);
  const showToastAction = useAppStore(state => state.showToast);
  const hideToastAction = useAppStore(state => state.hideToast);

  // 📊 Progress bar state (local, for animation performance)
  const [toastProgress, setToastProgress] = useState(100);

  // Wrap store actions for consistent API
  const showToast = useCallback(
    (message, severity = 'info') => {
      showToastAction(message, severity);
      setToastProgress(100); // Reset progress on new toast
    },
    [showToastAction]
  );

  const handleCloseToast = useCallback(() => {
    hideToastAction();
    setToastProgress(100);
  }, [hideToastAction]);

  // ✅ Progress bar animation using requestAnimationFrame
  useEffect(() => {
    if (!toast.open) {
      setToastProgress(100);
      return;
    }

    setToastProgress(100);
    const duration = 3500; // Matches autoHideDuration
    const startTime = performance.now();

    let animationId;

    const animate = () => {
      const elapsed = performance.now() - startTime;
      const progress = Math.max(0, 100 - (elapsed / duration) * 100);

      setToastProgress(progress);

      if (progress > 0 && elapsed < duration) {
        animationId = requestAnimationFrame(animate);
      }
    };

    animationId = requestAnimationFrame(animate);

    return () => {
      if (animationId) {
        cancelAnimationFrame(animationId);
      }
    };
  }, [toast.open, toast.message]); // Re-run animation on new message too

  return {
    toast,
    toastProgress,
    showToast,
    handleCloseToast,
  };
}
