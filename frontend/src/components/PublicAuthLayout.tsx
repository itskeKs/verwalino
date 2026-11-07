import { useEffect, useRef, type ReactNode } from "react";
import { House } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader } from "./ui/card";
import "./PublicAuthLayout.css";

export function PublicAuthLayout({ title, description, children }: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <main className="public-auth" aria-labelledby="public-auth-title">
      <div className="public-auth-layout">
        <header className="public-auth-brand">
          <div className="public-auth-wordmark">
            <span className="public-auth-mark"><House aria-hidden="true" /></span>
            <span>verwalino</span>
          </div>
          <div className="public-auth-context">
            <p>Immobilienverwaltung</p>
            <p>Mietverwaltung · WEG · Sondereigentum</p>
          </div>
        </header>
        <Card className="public-auth-card">
          <CardHeader className="public-auth-header">
            <h1 id="public-auth-title">{title}</h1>
            <CardDescription>{description}</CardDescription>
          </CardHeader>
          <CardContent className="public-auth-content">{children}</CardContent>
        </Card>
      </div>
    </main>
  );
}

export function PublicAuthMessage({ children, id, role = "alert" }: {
  children: string;
  id?: string;
  role?: "alert" | "status";
}) {
  const message = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    message.current?.focus();
  }, [children]);

  return <p ref={message} id={id} role={role} tabIndex={-1} className={`public-auth-message public-auth-message--${role}`}>{children}</p>;
}
