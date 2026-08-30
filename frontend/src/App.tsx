import { useState } from "react";
import { ProfileSelector, type UserProfile } from "./components/Onboarding/ProfileSelector";
import { HeatMap } from "./components/Map/HeatMap";

export default function App() {
  // Restore saved profile from localStorage (or null = show onboarding)
  const [profile, setProfile] = useState<UserProfile | null>(() => {
    try {
      const saved = localStorage.getItem("heatgraph_profile");
      return saved ? JSON.parse(saved) : null;
    } catch {
      return null;
    }
  });

  if (!profile) {
    return <ProfileSelector onComplete={(p) => setProfile(p)} />;
  }

  return <HeatMap initialProfile={profile} onChangeProfile={() => setProfile(null)} />;
}
