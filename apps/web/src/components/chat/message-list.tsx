"use client";

import { useEffect, useRef } from "react";

import { MessageBubble } from "@/components/chat/message-bubble";
import { WorkingIndicator } from "@/components/chat/working-indicator";
import type { DisplayMessage } from "@/lib/types/ui";

export function MessageList({
  messages,
  userId,
  pending,
}: {
  messages: DisplayMessage[];
  userId: string;
  pending: boolean;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, pending]);

  return (
    <div className="mx-auto flex w-full min-w-0 max-w-3xl flex-col gap-4 px-4 py-6">
      {messages.map((m, i) => (
        <MessageBubble key={i} message={m} userId={userId} />
      ))}
      {pending && <WorkingIndicator />}
      <div ref={bottomRef} />
    </div>
  );
}
