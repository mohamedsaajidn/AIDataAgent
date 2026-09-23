interface Props {
  userId: string
}

export default function UserBar({ userId }: Props) {
  return (
    <div className="user-bar">
      <span className="user-bar-label">Signed in as:</span>
      <span className="user-bar-value">{userId}</span>
      <span className="user-bar-hint">
        From this machine's .env.local (VITE_USER_ID) — no login in this POC. Set to a real auth
        identity once one exists.
      </span>
    </div>
  )
}
