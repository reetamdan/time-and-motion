import type { ReactNode } from 'react';

export function PageHeader({ eyebrow, title, description, actions, children }: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div className="page-heading-copy">
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p>{description}</p>
        {children}
      </div>
      {actions ? <div className="page-heading-actions">{actions}</div> : null}
    </div>
  );
}
