import { Link } from 'react-router-dom'
import type { ComponentProps, ReactNode } from 'react'

type Props = Omit<ComponentProps<typeof Link>, 'target' | 'rel'> & {
  children: ReactNode
}

/** Open destination in a new browser tab. */
export default function NewTabLink({ children, ...rest }: Props) {
  return (
    <Link {...rest} target="_blank" rel="noopener noreferrer">
      {children}
    </Link>
  )
}
