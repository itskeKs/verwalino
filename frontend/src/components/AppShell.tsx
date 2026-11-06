/**
 * Shared routed work with Klar and Warm navigation layouts.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FocusEvent, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Building2, Inbox, LayoutDashboard, X } from "lucide-react";
import { NavLink } from "react-router-dom";
import { useDesignTheme } from "../design/useDesignTheme";
import { OrganizationDeletionBanner } from "./OrganizationDeletionBanner";
import { Topbar } from "./Topbar";
import { Sidebar } from "./Sidebar";

interface Props {
  children: ReactNode;
}

export function AppShell({ children }: Props) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { theme } = useDesignTheme();
  const desktopQuery = theme === "warm" ? "(min-width: 1024px)" : "(min-width: 768px)";
  const [desktop, setDesktop] = useState(() => window.matchMedia(desktopQuery).matches);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const desktopNavRef = useRef<HTMLDivElement>(null);
  const navigationHadFocus = useRef(false);
  const restoreAfterResize = useRef(false);

  const restoreNavigationFocus = useCallback(() => {
    if (window.matchMedia(desktopQuery).matches) {
      const navigation = desktopNavRef.current;
      const link = navigation?.querySelector<HTMLAnchorElement>('a[aria-current="page"]')
        ?? navigation?.querySelector<HTMLAnchorElement>("a");
      for (let parent = link?.closest("details"); parent; parent = parent.parentElement?.closest("details")) {
        parent.open = true;
      }
      link?.focus();
    } else {
      toggleRef.current?.focus();
    }
  }, [desktopQuery]);

  useEffect(() => {
    const media = window.matchMedia(desktopQuery);
    const resize = () => {
      // Remember navigation focus before the old layout is removed.
      restoreAfterResize.current = navigationHadFocus.current;
      setDesktop(media.matches);
      setSidebarOpen(false);
    };
    if (media.matches !== desktop) resize();
    media.addEventListener("change", resize);
    return () => media.removeEventListener("change", resize);
  }, [desktopQuery, desktop]);

  useLayoutEffect(() => {
    if (restoreAfterResize.current) {
      restoreAfterResize.current = false;
      restoreNavigationFocus();
    }
  }, [desktop, restoreNavigationFocus]);

  return (
    <Dialog.Root open={!desktop && sidebarOpen} onOpenChange={setSidebarOpen}>
      <div
        className={`app-layout app-layout--${theme}`}
        data-design-theme={theme}
        data-navigation={desktop ? "desktop" : "mobile"}
        onFocusCapture={(event: FocusEvent<HTMLElement>) => {
          navigationHadFocus.current = event.target === toggleRef.current
            || !!desktopNavRef.current?.contains(event.target)
            || !!event.target.closest("#mobile-navigation");
        }}
      >
        {desktop ? (
          <div ref={desktopNavRef} className="flex min-h-0 shrink-0">
            <Sidebar onClose={() => setSidebarOpen(false)} />
          </div>
        ) : (
          <Dialog.Portal>
            <Dialog.Overlay
              className="fixed inset-0 z-30 bg-black/40"
              onClick={(event) => {
                // The backdrop can receive a click before Radix registers its outside-pointer listener.
                if (event.button === 0 && !event.ctrlKey) setSidebarOpen(false);
              }}
            />
            <Dialog.Content
              id="mobile-navigation"
              aria-describedby={undefined}
              className="navigation-drawer fixed inset-y-0 left-0 z-40 outline-none data-[state=open]:animate-[navigation-slide-in_200ms_ease-out] data-[state=closed]:animate-[navigation-slide-out_200ms_ease-in]"
              inert={!sidebarOpen}
              onFocusCapture={(event) => {
                if (event.target instanceof HTMLAnchorElement || event.target.tagName === "SUMMARY") {
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
                    className="navigation-close"
                  >
                    <X className="h-4 w-4" />
                  </Dialog.Close>
                )}
              />
            </Dialog.Content>
          </Dialog.Portal>
        )}

        <div className="app-workspace">
          <Topbar
            onToggleSidebar={() => setSidebarOpen((o) => !o)}
            navigationOpen={sidebarOpen && !desktop}
            navigationButtonRef={toggleRef}
          />
          <nav className="mobile-shortcuts" aria-label="Schnellnavigation">
            <NavLink to="/" end><LayoutDashboard aria-hidden="true" />Übersicht</NavLink>
            <NavLink to="/objekte"><Building2 aria-hidden="true" />Objekte</NavLink>
            <NavLink to="/posteingang"><Inbox aria-hidden="true" />Posteingang</NavLink>
          </nav>
          <main className="app-main">
            <div className="app-page"><OrganizationDeletionBanner />{children}</div>
          </main>
        </div>
      </div>
    </Dialog.Root>
  );
}
