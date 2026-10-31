import { type ReactNode } from "react";
import { Card, CardContent, CardDescription, CardHeader } from "./ui/card";

export function PasswordRecoveryCard({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center bg-background p-4 sm:p-6">
    <Card className="w-full min-w-0 max-w-lg">
      <CardHeader><h1 className="break-words text-2xl font-semibold">{title}</h1><CardDescription>{description}</CardDescription></CardHeader>
      <CardContent className="space-y-4">{children}</CardContent>
    </Card>
  </main>;
}
