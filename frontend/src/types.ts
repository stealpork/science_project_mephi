export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
}
export interface RecordDTO {
  id: string;
  version: number;
  created_at: string;
  updated_at: string;
}
export interface Account {
  user_id: string;
  email: string;
  email_verified: boolean;
  global_roles: string[];
  account_status: string;
}
export interface Tokens {
  access_token: string;
  refresh_token: string;
  account: Account;
  expires_in: number;
  refresh_expires_in: number;
  token_type: "bearer";
}
export interface MFAChallenge {
  requires_2fa: true;
  challenge_token: string;
  expires_in: number;
}
export interface Dictionary extends RecordDTO {
  name: string;
  kind: string;
  parent_id: string | null;
  active: boolean;
  skill_kind?: string;
}
export interface Contact {
  type: string;
  value: string;
  visibility: "public" | "authenticated" | "private";
}
export interface Experience {
  title: string;
  description?: string;
  source?: string;
  project_id?: string;
  completed?: boolean;
}
export interface Profile extends RecordDTO {
  user_id: string;
  first_name: string;
  last_name: string;
  bio: string;
  university_id: string | null;
  faculty_id: string | null;
  department_id: string | null;
  specialty_id: string | null;
  city_id: string | null;
  course: number | null;
  avatar_file_id: string | null;
  skills: Dictionary[];
  interests: Dictionary[];
  experience: Experience[];
  contacts: Contact[];
  viewer: { is_self: boolean; can_invite: boolean };
  is_favorited: boolean | null;
  missing_fields: string[];
  completeness: number | null;
}
export type Access = "public_open" | "public_invite" | "private_invite";
export type ProjectStatus =
  | "draft"
  | "recruiting"
  | "active"
  | "completed"
  | "archived";
export interface Project extends RecordDTO {
  creator_id: string;
  creator_name: string;
  owner_id: string | null;
  title: string;
  summary: string;
  description: string;
  research_goal: string;
  area_id: string | null;
  university_id: string | null;
  interest_ids: string[];
  required_skill_ids: string[];
  required_roles: string[];
  preferred_specialty_ids: string[];
  eligible_courses: number[];
  min_completed_projects: number | null;
  team_size: number;
  deadline: string | null;
  access_type: Access;
  status: ProjectStatus;
  moderation_status: string;
  results: { title?: string; url?: string }[];
  members_count: number;
  free_slots: number;
  is_favorited: boolean | null;
  viewer: {
    is_owner: boolean;
    is_member: boolean;
    can_manage: boolean;
    application_status: string | null;
    invitation_status: string | null;
    can_apply: boolean;
    cannot_apply_reason: string | null;
  };
}
export interface Member extends RecordDTO {
  user_id: string;
  role: string;
  display_name?: string;
  joined_at?: string;
}
export interface Application extends RecordDTO {
  project_id: string;
  user_id: string;
  requested_role: string;
  message: string;
  status: string;
  decision_reason?: string;
}
export interface Invitation extends RecordDTO {
  project_id: string;
  user_id: string;
  role: string;
  message: string;
  status: string;
  expires_at: string;
  project?: Pick<
    Project,
    "id" | "title" | "summary" | "owner_id" | "access_type"
  > | null;
}
export interface Author {
  author_id: string;
  user_id: string | null;
  display_name: string;
  affiliation: string;
  position: number;
  access_granted_at: string | null;
}
export interface Paper extends RecordDTO {
  creator_id: string;
  owner_id: string | null;
  title: string;
  abstract: string;
  area_id: string | null;
  university_id: string | null;
  keywords: string[];
  year: number | null;
  external_links: string[];
  project_id: string | null;
  file_id: string | null;
  status: "draft" | "published" | "archived";
  moderation_status: string;
  views_count: number;
  saves_count: number;
  authors: Author[];
  viewer: { can_edit: boolean; is_owner: boolean; can_manage_authors: boolean };
  is_favorited: boolean | null;
}
export interface Recommendation {
  project: Project;
  score: number | null;
  reasons: (string | { text?: string; label?: string })[];
  components: unknown;
  algorithm_version: string;
}
export interface RecommendationPage extends Page<Recommendation> {
  personalized: boolean;
  fallback_reason: string | null;
}
export interface UploadCreated {
  upload_id: string;
  file_id: string;
  upload_method: "PUT";
  upload_url: string;
  required_headers: { [key: string]: string };
  reservation_id: string | null;
  expires_at: string;
}
export interface UploadState {
  upload_id: string;
  file_id: string;
  status:
    | "created"
    | "uploaded"
    | "scanning"
    | "ready"
    | "rejected"
    | "expired"
    | "cancelled";
  rejection_code: string | null;
}
export interface ProjectFile extends RecordDTO {
  file_id: string;
  title: string;
  filename?: string;
  origin?: string;
  paper_id?: string;
}
export interface ProjectFiles {
  items: ProjectFile[];
  limit: number;
  used_slots: number;
  reserved_slots: number;
  can_upload: boolean;
}
export type FavoriteType = "user" | "project" | "paper";
export interface Favorite {
  type: FavoriteType;
  target_id: string;
  availability: string;
  card: Profile | Project | Paper | null;
  saved_at: string;
}
export interface Notification extends RecordDTO {
  type?: string;
  kind?: string;
  title?: string;
  body?: string;
  payload?: { [key: string]: unknown };
  read_at: string | null;
  target?: { type: string; id: string };
  action?: string;
}
export interface AdminRecord extends RecordDTO {
  [key: string]: unknown;
}
export interface Session extends RecordDTO {
  user_agent?: string;
  ip_address?: string;
  is_current?: boolean;
  revoked_at: string | null;
  expires_at: string;
}
