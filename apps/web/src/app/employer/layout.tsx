export default function EmployerLayout({children}:{children:React.ReactNode}) {
 if (!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY) return <main style={{padding:40}}>Employer authentication is not configured in this local demo. <a href="/">Return to ProofStack</a></main>;
 return children;
}
