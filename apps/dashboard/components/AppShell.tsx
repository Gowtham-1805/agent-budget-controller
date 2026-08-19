"use client";

import { useState, useCallback } from "react";
import { Sidebar } from "./Sidebar";
import { CommandPalette } from "./CommandPalette";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [searchOpen, setSearchOpen] = useState(false);

  const openSearch = useCallback(() => setSearchOpen(true), []);

  return (
    <>
      <Sidebar onOpenSearch={openSearch} />
      {children}
      <CommandPalette open={searchOpen} onOpenChange={setSearchOpen} />
    </>
  );
}
