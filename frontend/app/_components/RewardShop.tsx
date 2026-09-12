"use client";

import Image from "next/image";
import { useState } from "react";
import { CoinIcon } from "./Icons";

const balance = 12;
const rewards = [
  {
    name: "UpJob Essential Tee",
    description: "Heavyweight cotton with the UpJob mark embroidered at the chest.",
    price: 300,
    image: "/rewards/upjob-tee.png",
    imageAlt: "Cream UpJob t-shirt with a blue and orange embroidered arrow",
    tone: "cream",
  },
  {
    name: "Builder Cap",
    description: "A structured cotton cap for the next interview, meetup, or coffee run.",
    price: 1000,
    image: "/rewards/upjob-cap.png",
    imageAlt: "Cobalt blue UpJob cap with an orange embroidered arrow",
    tone: "blue",
  },
];

export function RewardShop() {
  const [saved, setSaved] = useState<string[]>([]);

  function toggleSaved(name: string) {
    setSaved((items) => items.includes(name) ? items.filter((item) => item !== name) : [...items, name]);
  }

  return (
    <div className="shop-page">
      <section className="shop-intro">
        <div><p className="eyebrow">Reward shop</p><h1>Progress looks good on you.</h1><p>Turn the momentum from your job search into something tangible.</p></div>
        <div className="balance-card"><span>Available balance</span><strong>{balance}</strong><div><CoinIcon /> UpJob coins</div></div>
      </section>

      <section aria-labelledby="items-heading">
        <div className="shop-section-heading"><div><p className="eyebrow">Merch</p><h2 id="items-heading">Items</h2></div><span>{rewards.length} rewards</span></div>
        <div className="product-grid">
          {rewards.map((reward) => {
            const needed = reward.price - balance;
            const isSaved = saved.includes(reward.name);
            return (
              <article className="product-card" key={reward.name}>
                <div className={`product-image product-image-${reward.tone}`}>
                  <Image src={reward.image} alt={reward.imageAlt} width={640} height={540} sizes="(max-width: 720px) 88vw, 520px" />
                  <button className={`save-button ${isSaved ? "is-saved" : ""}`} type="button" onClick={() => toggleSaved(reward.name)} aria-pressed={isSaved} aria-label={`${isSaved ? "Remove" : "Save"} ${reward.name}`}>
                    <span aria-hidden="true">{isSaved ? "♥" : "♡"}</span>
                  </button>
                </div>
                <div className="product-body">
                  <div className="product-copy"><h3>{reward.name}</h3><p>{reward.description}</p></div>
                  <div className="product-footer">
                    <strong className="product-price"><CoinIcon />{reward.price.toLocaleString()} <span>coins</span></strong>
                    <button className="redeem-button" type="button" disabled aria-describedby={`${reward.tone}-shortfall`}>Keep earning</button>
                  </div>
                  <p className="shortfall" id={`${reward.tone}-shortfall`}>{needed.toLocaleString()} more coins to redeem</p>
                </div>
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
