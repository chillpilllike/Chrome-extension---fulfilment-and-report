"use client"

import { useRef } from "react"
import type { InputHTMLAttributes } from "react"

import { cn } from "@/lib/utils"

let shiftPressed = false
let shiftTrackerInstalled = false

function ensureShiftTracker() {
  if (shiftTrackerInstalled || typeof window === "undefined") return
  shiftTrackerInstalled = true
  window.addEventListener("keydown", (event) => {
    if (event.key === "Shift") shiftPressed = true
  })
  window.addEventListener("keyup", (event) => {
    if (event.key === "Shift") shiftPressed = false
  })
  window.addEventListener("blur", () => {
    shiftPressed = false
  })
}

type CheckboxProps = Omit<InputHTMLAttributes<HTMLInputElement>, "onChange" | "type"> & {
  onCheckedChange?: (checked: boolean, event: { shiftKey: boolean }) => void
}

function Checkbox({ className, onCheckedChange, checked, onClick, onKeyDown, onKeyUp, onMouseDown, onMouseUp, onPointerDown, onPointerUp, ...props }: CheckboxProps) {
  ensureShiftTracker()
  const shiftKeyRef = useRef(false)
  const rememberShift = (shiftKey: boolean) => {
    if (shiftKey || shiftPressed) shiftKeyRef.current = true
  }

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
      onPointerDown={(event) => {
        rememberShift(event.shiftKey)
        onPointerDown?.(event)
      }}
      onPointerUp={(event) => {
        rememberShift(event.shiftKey)
        onPointerUp?.(event)
      }}
      onMouseDown={(event) => {
        rememberShift(event.shiftKey)
        onMouseDown?.(event)
      }}
      onMouseUp={(event) => {
        rememberShift(event.shiftKey)
        onMouseUp?.(event)
      }}
      onClick={(event) => {
        rememberShift(event.shiftKey)
        onClick?.(event)
        const shiftKey = Boolean(event.shiftKey || shiftKeyRef.current || shiftPressed)
        shiftKeyRef.current = false
        onCheckedChange?.(event.currentTarget.checked, { shiftKey })
      }}
      onKeyDown={(event) => {
        if (event.key === " " || event.key === "Enter") {
          shiftKeyRef.current = Boolean(event.shiftKey || shiftPressed)
        }
        onKeyDown?.(event)
      }}
      onKeyUp={(event) => {
        if (event.key === "Shift") shiftPressed = false
        onKeyUp?.(event)
      }}
      onChange={() => {}}
      {...props}
    />
  )
}

export { Checkbox }
