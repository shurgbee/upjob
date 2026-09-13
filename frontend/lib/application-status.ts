// Client-safe: shared by the server data layer (lib/jobs), the API route, and
// the client Dashboard. Kept out of the server-only lib/jobs module so the
// client bundle can import the status list and helpers.

// User-selectable application statuses. metadata.status is otherwise free text
// from the Gmail classifier. The first is the default for a fresh application.
export const APPLICATION_STATUSES = [
  "Applied",
  "Screening",
  "Interviewing",
  "Offer",
  "Rejected",
  "Withdrawn",
] as const;

export type ApplicationStatus = (typeof APPLICATION_STATUSES)[number];

export function isApplicationStatus(value: unknown): value is ApplicationStatus {
  return typeof value === "string" && (APPLICATION_STATUSES as readonly string[]).includes(value);
}
