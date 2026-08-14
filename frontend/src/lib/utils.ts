import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import type { Ref } from "vue";

/** 合并 Tailwind CSS 类名 */
export function cn(...inputs: ClassValue[]) {
	return twMerge(clsx(inputs));
}

type Updater<T> = T | ((previous: T) => T);

/** 更新响应式引用的值 */
export function valueUpdater<T>(updaterOrValue: Updater<T>, ref: Ref<T>) {
	ref.value =
		typeof updaterOrValue === "function"
			? (updaterOrValue as (previous: T) => T)(ref.value)
			: updaterOrValue;
}
