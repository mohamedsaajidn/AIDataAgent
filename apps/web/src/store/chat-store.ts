import { create } from "zustand";

import type { AskResponse, ChatMessageRecord } from "@/lib/types/api";
import type { DisplayMessage } from "@/lib/types/ui";

/**
 * The one genuine cross-cutting client state in this app - which datasets
 * are attached/selected, which session is active, which message list is
 * showing, and which dataset the Data Explorer's version panel is focused
 * on. Everything else (datasets, sessions, versions) is server state and
 * lives in TanStack Query hooks (src/hooks) instead - this store owns only
 * what several independent components need to agree on right now.
 */
interface ChatState {
  sessionId: string | null;
  attachedDatasetIds: string[];
  selectedDatasetIds: string[];
  focusedVersionDatasetId: string | null;
  versionSidebarOpen: boolean;
  messages: DisplayMessage[];

  toggleSelectedDataset: (id: string) => void;
  clearSelectedDatasets: () => void;
  setFocusedVersionDataset: (id: string | null) => void;
  setVersionSidebarOpen: (open: boolean) => void;
  startNewChat: () => void;
  loadSession: (sessionId: string, datasetIds: string[], messages: ChatMessageRecord[]) => void;
  appendUserMessage: (content: string) => void;
  appendAssistantMessage: (res: AskResponse) => void;
  appendErrorMessage: (message: string) => void;
}

function fromHistory(m: ChatMessageRecord): DisplayMessage {
  return {
    id: m.id,
    role: m.role,
    content: m.content,
    route: m.route,
    code: m.code,
    result: m.result,
    hasArtifact: m.has_artifact,
    diff: m.diff,
    pendingTransformId: m.pending_transform_id,
    pendingTransformStatus: m.pending_transform_status,
    error: m.error,
    createdAt: m.created_at,
  };
}

function fromAskResponse(res: AskResponse): DisplayMessage {
  return {
    id: res.message_id,
    role: "assistant",
    content: res.answer,
    route: res.route,
    code: res.code,
    result: res.result,
    hasArtifact: res.has_artifact,
    diff: res.diff,
    pendingTransformId: res.pending_transform_id,
    rememberedFact: res.remembered_fact,
    error: res.error,
  };
}

export const useChatStore = create<ChatState>((set) => ({
  sessionId: null,
  attachedDatasetIds: [],
  selectedDatasetIds: [],
  focusedVersionDatasetId: null,
  versionSidebarOpen: true,
  messages: [],

  toggleSelectedDataset: (id) =>
    set((state) => {
      if (state.attachedDatasetIds.includes(id)) return state;
      const isSelected = state.selectedDatasetIds.includes(id);
      return {
        selectedDatasetIds: isSelected
          ? state.selectedDatasetIds.filter((x) => x !== id)
          : [...state.selectedDatasetIds, id],
      };
    }),

  clearSelectedDatasets: () => set({ selectedDatasetIds: [] }),

  setFocusedVersionDataset: (id) => set({ focusedVersionDatasetId: id }),

  setVersionSidebarOpen: (open) => set({ versionSidebarOpen: open }),

  startNewChat: () =>
    set({
      sessionId: null,
      attachedDatasetIds: [],
      selectedDatasetIds: [],
      messages: [],
    }),

  loadSession: (sessionId, datasetIds, messages) =>
    set({
      sessionId,
      attachedDatasetIds: datasetIds,
      selectedDatasetIds: [],
      messages: messages.map(fromHistory),
      focusedVersionDatasetId: datasetIds[0] ?? null,
    }),

  appendUserMessage: (content) =>
    set((state) => ({ messages: [...state.messages, { role: "user", content }] })),

  appendAssistantMessage: (res) =>
    set((state) => ({
      sessionId: res.session_id,
      attachedDatasetIds: res.dataset_ids,
      selectedDatasetIds: [],
      focusedVersionDatasetId: res.resolved_dataset_id ?? state.focusedVersionDatasetId,
      messages: [...state.messages, fromAskResponse(res)],
    })),

  appendErrorMessage: (message) =>
    set((state) => ({
      messages: [...state.messages, { role: "assistant", content: "", error: message }],
    })),
}));
