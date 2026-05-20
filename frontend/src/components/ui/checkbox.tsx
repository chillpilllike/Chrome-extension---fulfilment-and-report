"use client"

import type { InputHTMLAttributes } from "react"

import { cn } from "@/lib/utils"

type CheckboxProps = Omit<InputHTMLAttributes<HTMLInputElement>, "onChange" | "type"> & {
  onCheckedChange?: (checked: boolean, event: { shiftKey: boolean }) => void
}

function Checkbox({ className, onCheckedChange, checked, ...props }: CheckboxProps) {
  return (
    <input
      type="checkbox"
      data-slot="checkbox"
      className={cn(
        "form-check-input nutricity-checkbox",
        className
      )}
      checked={checked}
      data-checked={checked ? "true" : undefined}
      onChange={(event) => onCheckedChange?.(event.currentTarget.checked, { shiftKey: Boolean((event.nativeEvent as MouseEvent).shiftKey) })}
      {...props}
    />
  )
}

export { Checkbox }
