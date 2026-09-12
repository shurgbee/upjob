import type { Metadata } from "next";
import { AppShell } from "../_components/AppShell";
import { RewardShop } from "../_components/RewardShop";

export const metadata: Metadata = {
  title: "Reward Shop",
  description: "Redeem UpJob coins for progress-powered rewards.",
};

export default function ShopPage() {
  return <AppShell><RewardShop /></AppShell>;
}
