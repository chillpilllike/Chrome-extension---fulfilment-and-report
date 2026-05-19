import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "btn",
  {
    variants: {
      variant: {
        default: "btn-primary",
        outline: "btn-outline-secondary",
        secondary: "btn-secondary",
        ghost: "btn-ghost-secondary",
        destructive: "btn-danger",
        success: "btn-success",
        warning: "btn-warning",
        info: "btn-info",
        dark: "btn-dark",
        light: "btn-light",
        link: "btn-link",
      },
      size: {
        default: "",
        xs: "btn-sm",
        sm: "btn-sm",
        lg: "btn-lg",
        icon: "btn-icon",
        "icon-xs": "btn-icon btn-sm",
        "icon-sm": "btn-icon btn-sm",
        "icon-lg": "btn-icon btn-lg",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
