import type { Command } from '../../commands.js'

const buddy = {
  type: 'local-jsx',
  name: 'buddy',
  description: 'Inspect or interact with your companion',
  argumentHint: '[pet|mute|unmute|help]',
  load: () => import('./buddy.js'),
} satisfies Command

export default buddy
