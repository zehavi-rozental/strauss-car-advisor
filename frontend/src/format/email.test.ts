import { describe, expect, it } from 'vitest'
import { validEmail } from './email'

describe('validEmail: the server rule (10/11)', () => {
  it('takes a plain address of any domain', () => {
    for (const ok of ['dana.levi@example.com', 'Yossi.Cohen+cars@Example.co.il', "o'neil@mail.example.org"]) {
      expect(validEmail(ok)).toBe(true)
    }
  })
  it('refuses an address with a name, comments or no domain', () => {
    for (const bad of ['x<victim@example.com>', 'victim@example.com(c)', 'a@b', 'two@@example.com', 'name@example', '']) {
      expect(validEmail(bad)).toBe(false)
    }
  })
})
