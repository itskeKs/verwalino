/**
 * Application shell (UI-Kit): dark sidebar rail + topbar + scrolling content.
 * Desktop: sidebar static. Mobile: off-canvas sidebar with a dimming overlay.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FocusEvent, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { OrganizationDeletionBanner } from "./OrganizationDeletionBanner";
import { Topbar } from "./Topbar";
import { Sidebar } from "./Sidebar";

interface Props {
  children: ReactNode;
}

const DESKTOP_MEDIA_QUERY = "(min-width: 768px)";

export function AppShell({ children }: Props) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [desktop, setDesktop] = useState(() => window.matchMedia(DESKTOP_MEDIA_QUERY).matches);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const desktopNavRef = useRef<HTMLDivElement>(null);
  const navigationHadFocus = useRef(false);
  const restoreAfterResize = useRef(false);

  const restoreNavigationFocus = useCallback(() => {
    if (window.matchMedia(DESKTOP_MEDIA_QUERY).matches) {
      const navigation = desktopNavRef.current;
      (navigation?.querySelector<HTMLAnchorElement>('a[aria-current="page"]')
        ?? navigation?.querySelector<HTMLAnchorElement>("a"))?.focus();
    } else {
      toggleRef.current?.focus();
    }
  }, []);

  useEffect(() => {
    const media = window.matchMedia(DESKTOP_MEDIA_QUERY);
    const resize = (event: MediaQueryListEvent) => {
      // CSS can blur the disappearing toggle before the media change is delivered.
      restoreAfterResize.current = navigationHadFocus.current;
      setDesktop(event.matches);
      setSidebarOpen(false);
    };
    media.addEventListener("change", resize);
    return () => media.removeEventListener("change", resize);
  }, []);

  useLayoutEffect(() => {
    if (restoreAfterResize.current) {
      restoreAfterResize.current = false;
      restoreNavigationFocus();
    }
  }, [desktop, restoreNavigationFocus]);

  return (
    <Dialog.Root open={!desktop && sidebarOpen} onOpenChange={setSidebarOpen}>
    <div
      className="flex h-screen overflow-hidden bg-background"
      onFocusCapture={(event: FocusEvent<HTMLElement>) => {
        navigationHadFocus.current = event.target === toggleRef.current
          || !!desktopNavRef.current?.contains(event.target);
      }}
    >
      {desktop ? (
        <div ref={desktopNavRef} className="flex shrink-0">
          <Sidebar onClose={() => setSidebarOpen(false)} />
        </div>
      ) : (
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-30 bg-black/40" />
          <Dialog.Content
            id="mobile-navigation"
            aria-describedby={undefined}
            className="fixed inset-y-0 left-0 z-40 outline-none data-[state=open]:animate-[navigation-slide-in_200ms_ease-out] data-[state=closed]:animate-[navigation-slide-out_200ms_ease-in]"
            inert={!sidebarOpen}
            onFocusCapture={(event) => {
              if (event.target instanceof HTMLAnchorElement) {
                event.target.scrollIntoView({ block: "nearest" });
              }
            }}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              restoreNavigationFocus();
            }}
          >
            <Dialog.Title className="sr-only">Navigation</Dialog.Title>
            <Sidebar
              onClose={() => setSidebarOpen(false)}
              closeControl={(
                <Dialog.Close
                  aria-label="Navigation schließen"
                  className="ml-auto flex h-8 w-8 shrink-0 items-center justify-center rounded-md hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-sidebar-foreground"
                >
                  <X className="h-4 w-4" />
                </Dialog.Close>
              )}
            />
          </Dialog.Content>
        </Dialog.Portal>
      )}

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <Topbar
          onToggleSidebar={() => setSidebarOpen((o) => !o)}
          navigationOpen={sidebarOpen && !desktop}
          navigationButtonRef={toggleRef}
        />
        <main className="flex-1 overflow-y-auto px-4 py-6 md:px-8 md:py-8">
          <div className="mx-auto max-w-6xl"><OrganizationDeletionBanner />{children}</div>
        </main>
      </div>
    </div>
    </Dialog.Root>
  );
}
